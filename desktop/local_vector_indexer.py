"""
Hydra Desktop Local Vector Indexer Subsystem.
Implements pure-Python cosine similarity search, chunk embedding indexing,
top-k nearest neighbor retrieval, and metadata filtering without external dependencies.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


def vector_dot(v1: List[float], v2: List[float]) -> float:
    """Compute dot product between two float vectors."""
    return sum(a * b for a, b in zip(v1, v2))


def vector_norm(v: List[float]) -> float:
    """Compute Euclidean L2 norm of vector."""
    return math.sqrt(sum(x * x for x in v))


def vector_normalize(v: List[float]) -> List[float]:
    """Normalize vector to unit length."""
    norm = vector_norm(v)
    if norm < 1e-12:
        return [0.0] * len(v)
    return [x / norm for x in v]


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    norm1 = vector_norm(v1)
    norm2 = vector_norm(v2)
    if norm1 < 1e-12 or norm2 < 1e-12:
        return 0.0
    return vector_dot(v1, v2) / (norm1 * norm2)


def generate_text_embedding(text: str, dim: int = 64) -> List[float]:
    """
    Generate deterministic unit-normalized embedding vector using hashing trick over character n-grams.
    Provides fast, pure-Python semantic vector representation for testing and offline local search.
    """
    vec = [0.0] * dim
    words = text.lower().split()
    for word in words:
        # Word hash
        h = int(hashlib.sha256(word.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dim
        vec[idx] += 1.0

        # Subword character n-grams
        for n in range(3, min(6, len(word) + 1)):
            ngram = word[:n]
            h_ng = int(hashlib.md5(ngram.encode("utf-8")).hexdigest()[:8], 16)
            idx_ng = h_ng % dim
            vec[idx_ng] += 0.5

    return vector_normalize(vec)


@dataclass
class VectorDocumentChunk:
    """Indexed document chunk with vector embedding and metadata."""
    chunk_id: str
    text: str
    embedding: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "embedding": list(self.embedding),
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VectorDocumentChunk:
        return cls(
            chunk_id=data["chunk_id"],
            text=data["text"],
            embedding=data["embedding"],
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class SearchResult:
    """Top-k retrieval candidate with cosine similarity score."""
    chunk_id: str
    text: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "score": round(self.score, 4),
            "metadata": dict(self.metadata),
        }


class LocalVectorIndexer:
    """
    Pure-Python in-memory and disk-persisted vector indexer.
    Supports top-k nearest neighbor retrieval, metadata filtering, and JSON persistence.
    """

    def __init__(
        self,
        embedding_dim: int = 64,
        storage_path: Optional[str] = None,
    ) -> None:
        self.embedding_dim = embedding_dim
        self.storage_path = storage_path
        self._lock = threading.RLock()
        self._chunks: Dict[str, VectorDocumentChunk] = {}

    @property
    def total_chunks(self) -> int:
        with self._lock:
            return len(self._chunks)

    def add_chunk(
        self,
        chunk_id: str,
        text: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VectorDocumentChunk:
        """Insert or update a document chunk in the vector index."""
        emb = embedding or generate_text_embedding(text, dim=self.embedding_dim)
        chunk = VectorDocumentChunk(
            chunk_id=chunk_id,
            text=text,
            embedding=emb,
            metadata=metadata or {},
        )
        with self._lock:
            self._chunks[chunk_id] = chunk
        return chunk

    def add_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        """Batch insert document chunks."""
        with self._lock:
            count = 0
            for item in chunks:
                self.add_chunk(
                    chunk_id=item["chunk_id"],
                    text=item["text"],
                    embedding=item.get("embedding"),
                    metadata=item.get("metadata"),
                )
                count += 1
            return count

    def get_chunk(self, chunk_id: str) -> Optional[VectorDocumentChunk]:
        """Fetch chunk by ID."""
        with self._lock:
            return self._chunks.get(chunk_id)

    def delete_chunk(self, chunk_id: str) -> bool:
        """Delete chunk by ID."""
        with self._lock:
            return self._chunks.pop(chunk_id, None) is not None

    def clear(self) -> None:
        """Purge all indexed chunks."""
        with self._lock:
            self._chunks.clear()

    def search(
        self,
        query: Union[str, List[float]],
        top_k: int = 5,
        min_score: float = -1.0,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """
        Perform cosine similarity search over indexed chunks with optional metadata filtering.
        """
        if isinstance(query, str):
            q_vec = generate_text_embedding(query, dim=self.embedding_dim)
        else:
            q_vec = vector_normalize(query)

        candidates: List[SearchResult] = []

        with self._lock:
            for chunk in self._chunks.values():
                # Apply metadata filtering
                if filter_metadata:
                    match = True
                    for fk, fv in filter_metadata.items():
                        if chunk.metadata.get(fk) != fv:
                            match = False
                            break
                    if not match:
                        continue

                score = cosine_similarity(q_vec, chunk.embedding)
                if score >= min_score:
                    candidates.append(
                        SearchResult(
                            chunk_id=chunk.chunk_id,
                            text=chunk.text,
                            score=score,
                            metadata=chunk.metadata,
                        )
                    )

        # Sort descending by score
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates[:top_k]

    def export_to_json(self) -> str:
        """Export vector index state to JSON string."""
        with self._lock:
            data = {
                "embedding_dim": self.embedding_dim,
                "chunks": [c.to_dict() for c in self._chunks.values()],
            }
            return json.dumps(data, indent=2)

    def import_from_json(self, json_str: str) -> int:
        """Import vector index state from JSON string."""
        with self._lock:
            data = json.loads(json_str)
            self.embedding_dim = data.get("embedding_dim", self.embedding_dim)
            count = 0
            for item in data.get("chunks", []):
                chunk = VectorDocumentChunk.from_dict(item)
                self._chunks[chunk.chunk_id] = chunk
                count += 1
            return count

    def save_to_disk(self, filepath: Optional[str] = None) -> str:
        """Persist index state to JSON file."""
        target = filepath or self.storage_path
        if not target:
            raise ValueError("No storage path specified")
        with self._lock:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(self.export_to_json())
            return target

    def load_from_disk(self, filepath: Optional[str] = None) -> int:
        """Load index state from JSON file."""
        target = filepath or self.storage_path
        if not target or not os.path.isfile(target):
            raise FileNotFoundError(f"Vector index storage file not found: {target}")
        with self._lock:
            with open(target, "r", encoding="utf-8") as f:
                return self.import_from_json(f.read())


_GLOBAL_VECTOR_INDEXER: Optional[LocalVectorIndexer] = None
_GLOBAL_INDEXER_LOCK = threading.RLock()


def get_local_vector_indexer() -> LocalVectorIndexer:
    """Acquire thread-safe singleton LocalVectorIndexer."""
    global _GLOBAL_VECTOR_INDEXER
    with _GLOBAL_INDEXER_LOCK:
        if _GLOBAL_VECTOR_INDEXER is None:
            _GLOBAL_VECTOR_INDEXER = LocalVectorIndexer()
        return _GLOBAL_VECTOR_INDEXER


def reset_local_vector_indexer() -> LocalVectorIndexer:
    """Reset singleton LocalVectorIndexer."""
    global _GLOBAL_VECTOR_INDEXER
    with _GLOBAL_INDEXER_LOCK:
        _GLOBAL_VECTOR_INDEXER = LocalVectorIndexer()
        return _GLOBAL_VECTOR_INDEXER
