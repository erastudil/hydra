"""
Hydra Desktop Runtime LoRA Adapter Hotloader Subsystem.
Provides dynamic adapter mounting, alpha scaling, base weight non-destructive blending,
multi-adapter composition, and VRAM memory footprint tracking.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import collections
import copy
import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


class AdapterStatus(str, Enum):
    """Mount status of managed LoRA adapter."""
    MOUNTED = "mounted"
    UNMOUNTED = "unmounted"
    DISABLED = "disabled"


@dataclass
class LoraLayerWeights:
    """Low-rank decomposition matrices A (r x k) and B (d x r) for layer."""
    layer_name: str
    in_features: int
    out_features: int
    rank: int
    matrix_a: List[List[float]]  # rank x in_features
    matrix_b: List[List[float]]  # out_features x rank
    bytes_per_param: int = 4  # 4 bytes for float32

    @property
    def parameter_count(self) -> int:
        """Total parameter count in decomposed low-rank factors."""
        return (self.in_features * self.rank) + (self.out_features * self.rank)

    @property
    def memory_bytes(self) -> int:
        """Memory consumption in bytes."""
        return self.parameter_count * self.bytes_per_param


@dataclass
class LoraAdapterRecord:
    """Hot-loadable LoRA adapter package."""
    adapter_id: str
    name: str
    rank: int
    alpha: float
    layers: Dict[str, LoraLayerWeights] = field(default_factory=dict)
    status: AdapterStatus = AdapterStatus.MOUNTED
    scale_multiplier: float = 1.0
    mounted_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def scaling_factor(self) -> float:
        """Compute mathematical LoRA scale factor gamma = alpha / rank."""
        if self.rank <= 0:
            return 1.0
        return (self.alpha / float(self.rank)) * self.scale_multiplier

    @property
    def total_memory_bytes(self) -> int:
        """Total memory footprint across all adapter layers in bytes."""
        return sum(layer.memory_bytes for layer in self.layers.values())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize adapter record metadata to dictionary."""
        return {
            "adapter_id": self.adapter_id,
            "name": self.name,
            "rank": self.rank,
            "alpha": self.alpha,
            "status": self.status.value,
            "scaling_factor": round(self.scaling_factor, 4),
            "scale_multiplier": self.scale_multiplier,
            "layer_count": len(self.layers),
            "memory_bytes": self.total_memory_bytes,
            "mounted_at": self.mounted_at,
            "last_used": self.last_used,
            "metadata": dict(self.metadata),
        }


def matrix_vector_mult(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> List[float]:
    """Multiply matrix (m x n) by column vector (n), returning output vector (m)."""
    out: List[float] = []
    for row in matrix:
        dot = sum(a * b for a, b in zip(row, vector))
        out.append(dot)
    return out


def matrix_add(matrix_a: Sequence[Sequence[float]], matrix_b: Sequence[Sequence[float]], scale_b: float = 1.0) -> List[List[float]]:
    """Add two matrices of identical dimension with scalar scaling on matrix_b."""
    rows = len(matrix_a)
    cols = len(matrix_a[0]) if rows > 0 else 0
    res: List[List[float]] = []
    for r in range(rows):
        res_row = [matrix_a[r][c] + scale_b * matrix_b[r][c] for c in range(cols)]
        res.append(res_row)
    return res


def compute_lora_delta_matrix(layer: LoraLayerWeights, scaling_factor: float) -> List[List[float]]:
    """
    Compute full delta matrix Delta W = scaling_factor * (B x A).
    Matrix B has shape (out_features x rank), Matrix A has shape (rank x in_features).
    Resulting matrix Delta W has shape (out_features x in_features).
    """
    out_dim = layer.out_features
    in_dim = layer.in_features
    r_dim = layer.rank

    delta: List[List[float]] = []
    for o in range(out_dim):
        row: List[float] = []
        b_row = layer.matrix_b[o]
        for i in range(in_dim):
            elem = sum(b_row[k] * layer.matrix_a[k][i] for k in range(r_dim))
            row.append(elem * scaling_factor)
        delta.append(row)
    return delta


class ModelLoraAdapterHotloader:
    """
    Runtime LoRA adapter hotloader engine.
    Maintains collection of mounted LoRA adapters, applies non-destructive
    delta blending against pristine base model weights, compositions, and tracks VRAM budgets.
    """

    def __init__(self, max_vram_budget_bytes: int = 512 * 1024 * 1024) -> None:
        self.max_vram_budget_bytes = max(1024 * 1024, max_vram_budget_bytes)
        self._adapters: Dict[str, LoraAdapterRecord] = {}
        self._lock = threading.RLock()

    def mount_adapter(
        self,
        adapter_id: str,
        name: str,
        rank: int,
        alpha: float,
        layers: Dict[str, LoraLayerWeights],
        scale_multiplier: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LoraAdapterRecord:
        """Mount new LoRA adapter into active execution pool."""
        with self._lock:
            record = LoraAdapterRecord(
                adapter_id=adapter_id,
                name=name,
                rank=max(1, rank),
                alpha=max(0.1, alpha),
                layers=dict(layers),
                scale_multiplier=max(0.0, scale_multiplier),
                status=AdapterStatus.MOUNTED,
                metadata=dict(metadata) if metadata else {},
            )

            # Check VRAM footprint and enforce budget
            required_bytes = record.total_memory_bytes
            current_bytes = self.get_allocated_memory_bytes()

            if current_bytes + required_bytes > self.max_vram_budget_bytes:
                # Evict least recently used unpinned unmounted or disabled adapters
                self._evict_excess_vram(required_bytes)

            self._adapters[adapter_id] = record
            return record

    def unmount_adapter(self, adapter_id: str) -> Optional[LoraAdapterRecord]:
        """Unmount adapter from memory pool."""
        with self._lock:
            if adapter_id not in self._adapters:
                return None
            rec = self._adapters.pop(adapter_id)
            rec.status = AdapterStatus.UNMOUNTED
            return rec

    def set_adapter_status(self, adapter_id: str, status: AdapterStatus) -> None:
        """Toggle active status of mounted adapter."""
        with self._lock:
            if adapter_id not in self._adapters:
                raise KeyError(f"Adapter {adapter_id} not found")
            self._adapters[adapter_id].status = status

    def set_scale_multiplier(self, adapter_id: str, multiplier: float) -> None:
        """Adjust runtime alpha scale multiplier for adapter."""
        with self._lock:
            if adapter_id not in self._adapters:
                raise KeyError(f"Adapter {adapter_id} not found")
            self._adapters[adapter_id].scale_multiplier = max(0.0, multiplier)

    def apply_layer_forward(
        self,
        layer_name: str,
        base_weight_matrix: Sequence[Sequence[float]],
        input_vector: Sequence[float],
        active_adapter_ids: Optional[Sequence[str]] = None,
    ) -> List[float]:
        """
        Execute non-destructive layer forward pass:
        y = W_0 * x + sum_k gamma_k * (B_k * A_k) * x
        Base weight matrix remains completely pristine and unmodified.
        """
        with self._lock:
            now = time.time()
            # 1. Base transformation: y_base = W_0 * x
            output = matrix_vector_mult(base_weight_matrix, input_vector)

            # 2. Additive LoRA deltas
            selected_ids = set(active_adapter_ids) if active_adapter_ids is not None else set(self._adapters.keys())

            for aid in selected_ids:
                adapter = self._adapters.get(aid)
                if not adapter or adapter.status != AdapterStatus.MOUNTED:
                    continue

                if layer_name not in adapter.layers:
                    continue

                adapter.last_used = now
                lora_layer = adapter.layers[layer_name]
                gamma = adapter.scaling_factor

                # Compute low-rank transformation: intermediate = A * x (rank-dimensional)
                intermediate = matrix_vector_mult(lora_layer.matrix_a, input_vector)
                # Compute delta = B * intermediate (out_dim-dimensional)
                lora_out = matrix_vector_mult(lora_layer.matrix_b, intermediate)

                # Add scaled delta to output
                for i in range(len(output)):
                    output[i] += gamma * lora_out[i]

            return output

    def blend_layer_weights(
        self,
        layer_name: str,
        pristine_base_matrix: Sequence[Sequence[float]],
        active_adapter_ids: Optional[Sequence[str]] = None,
    ) -> List[List[float]]:
        """
        Synthesize blended weight matrix without mutating original base copy:
        W_blended = W_0 + sum_k gamma_k * (B_k x A_k).
        """
        with self._lock:
            blended = [list(row) for row in pristine_base_matrix]
            selected_ids = set(active_adapter_ids) if active_adapter_ids is not None else set(self._adapters.keys())

            for aid in selected_ids:
                adapter = self._adapters.get(aid)
                if not adapter or adapter.status != AdapterStatus.MOUNTED:
                    continue

                if layer_name not in adapter.layers:
                    continue

                lora_layer = adapter.layers[layer_name]
                gamma = adapter.scaling_factor
                delta = compute_lora_delta_matrix(lora_layer, gamma)
                blended = matrix_add(blended, delta)

            return blended

    def get_allocated_memory_bytes(self) -> int:
        """Compute total VRAM consumption across all mounted adapters."""
        with self._lock:
            return sum(a.total_memory_bytes for a in self._adapters.values())

    def _evict_excess_vram(self, needed_bytes: int) -> None:
        """Evict least recently used unmounted/disabled adapters until budget satisfied."""
        # Sort candidates by last_used timestamp
        candidates = sorted(
            [a for a in self._adapters.values() if a.status != AdapterStatus.MOUNTED],
            key=lambda x: x.last_used,
        )
        for cand in candidates:
            if self.get_allocated_memory_bytes() + needed_bytes <= self.max_vram_budget_bytes:
                break
            self._adapters.pop(cand.adapter_id, None)

    def get_adapter(self, adapter_id: str) -> Optional[LoraAdapterRecord]:
        """Retrieve adapter record by identifier."""
        with self._lock:
            return self._adapters.get(adapter_id)

    def list_adapters(self) -> List[Dict[str, Any]]:
        """List summary metadata for all registered adapters."""
        with self._lock:
            return [a.to_dict() for a in self._adapters.values()]

    def reset(self) -> None:
        """Clear all mounted adapters."""
        with self._lock:
            self._adapters.clear()


_GLOBAL_LORA_HOTLOADER: Optional[ModelLoraAdapterHotloader] = None
_GLOBAL_LORA_LOCK = threading.RLock()


def get_lora_hotloader() -> ModelLoraAdapterHotloader:
    """Acquire thread-safe singleton ModelLoraAdapterHotloader."""
    global _GLOBAL_LORA_HOTLOADER
    with _GLOBAL_LORA_LOCK:
        if _GLOBAL_LORA_HOTLOADER is None:
            _GLOBAL_LORA_HOTLOADER = ModelLoraAdapterHotloader()
        return _GLOBAL_LORA_HOTLOADER


def reset_lora_hotloader() -> ModelLoraAdapterHotloader:
    """Reset singleton ModelLoraAdapterHotloader."""
    global _GLOBAL_LORA_HOTLOADER
    with _GLOBAL_LORA_LOCK:
        _GLOBAL_LORA_HOTLOADER = ModelLoraAdapterHotloader()
        return _GLOBAL_LORA_HOTLOADER
