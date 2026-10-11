"""
Hydra Desktop Model Quantization Evaluator Subsystem.
Calculates GGUF quantization tensor sizes, bits-per-weight (bpw) estimates,
KV cache context scaling footprints, and host RAM / GPU VRAM offloading requirements.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


class QuantizationType(str, Enum):
    """Standard GGUF / llama.cpp quantization types."""
    FP16 = "F16"
    Q8_0 = "Q8_0"
    Q6_K = "Q6_K"
    Q5_K_M = "Q5_K_M"
    Q5_K_S = "Q5_K_S"
    Q4_K_M = "Q4_K_M"
    Q4_K_S = "Q4_K_S"
    Q4_0 = "Q4_0"
    Q3_K_M = "Q3_K_M"
    Q3_K_S = "Q3_K_S"
    Q2_K = "Q2_K"
    IQ4_XS = "IQ4_XS"
    IQ3_XXS = "IQ3_XXS"
    IQ2_XXS = "IQ2_XXS"


# Bits-per-weight estimates for each quantization format
GGUF_BPW_MAP: Dict[QuantizationType, float] = {
    QuantizationType.FP16: 16.0,
    QuantizationType.Q8_0: 8.5,
    QuantizationType.Q6_K: 6.56,
    QuantizationType.Q5_K_M: 5.5,
    QuantizationType.Q5_K_S: 5.3,
    QuantizationType.Q4_K_M: 4.5,
    QuantizationType.Q4_K_S: 4.3,
    QuantizationType.Q4_0: 4.5,
    QuantizationType.Q3_K_M: 3.4,
    QuantizationType.Q3_K_S: 3.2,
    QuantizationType.Q2_K: 2.6,
    QuantizationType.IQ4_XS: 4.25,
    QuantizationType.IQ3_XXS: 3.06,
    QuantizationType.IQ2_XXS: 2.06,
}


class OffloadStrategy(str, Enum):
    """GPU / CPU offload execution strategy."""
    FULL_GPU = "FULL_GPU"
    PARTIAL_OFFLOAD = "PARTIAL_OFFLOAD"
    CPU_ONLY = "CPU_ONLY"
    INSUFFICIENT_MEMORY = "INSUFFICIENT_MEMORY"


@dataclass
class ModelArchitectureProfile:
    """Transformer architecture parameters for memory footprint calculation."""
    name: str
    param_count: int
    num_layers: int
    num_kv_heads: int
    head_dim: int
    default_context_length: int = 8192

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "param_count": self.param_count,
            "num_layers": self.num_layers,
            "num_kv_heads": self.num_kv_heads,
            "head_dim": self.head_dim,
            "default_context_length": self.default_context_length,
        }


BUILTIN_MODEL_PROFILES: Dict[str, ModelArchitectureProfile] = {
    "llama-3.1-8b": ModelArchitectureProfile("llama-3.1-8b", 8_030_000_000, 32, 8, 128, 8192),
    "llama-3.1-70b": ModelArchitectureProfile("llama-3.1-70b", 70_600_000_000, 80, 8, 128, 8192),
    "gemma-2-9b": ModelArchitectureProfile("gemma-2-9b", 9_240_000_000, 42, 8, 256, 8192),
    "gemma-2-27b": ModelArchitectureProfile("gemma-2-27b", 27_200_000_000, 46, 16, 128, 8192),
    "qwen-2.5-7b": ModelArchitectureProfile("qwen-2.5-7b", 7_610_000_000, 28, 4, 128, 8192),
    "qwen-2.5-14b": ModelArchitectureProfile("qwen-2.5-14b", 14_700_000_000, 48, 8, 128, 8192),
    "qwen-2.5-32b": ModelArchitectureProfile("qwen-2.5-32b", 32_500_000_000, 64, 8, 128, 8192),
}


def calculate_weight_size_bytes(param_count: int, bpw: float) -> int:
    """Calculate raw tensor weight size in bytes from parameter count and bpw."""
    return int((param_count * bpw) / 8.0)


def calculate_kv_cache_bytes(
    num_layers: int,
    num_kv_heads: int,
    head_dim: int,
    context_length: int,
    bytes_per_element: float = 2.0,
) -> int:
    """
    Calculate KV cache allocation in bytes.
    Formula: 2 (Key + Value) * layers * kv_heads * head_dim * context_tokens * bytes_per_element.
    """
    return int(2 * num_layers * num_kv_heads * head_dim * context_length * bytes_per_element)


@dataclass
class QuantizationEvaluationResult:
    """Comprehensive memory and hardware fit evaluation result for a quantized model."""
    model_name: str
    quant_type: str
    bpw: float
    context_length: int
    tensor_size_mb: float
    kv_cache_size_mb: float
    overhead_mb: float
    total_required_mb: float
    available_vram_mb: float
    available_ram_mb: float
    fits_in_vram: bool
    fits_in_ram: bool
    recommended_gpu_layers: int
    total_layers: int
    offload_strategy: OffloadStrategy
    headroom_mb: float
    estimated_speed_relative: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "quant_type": self.quant_type,
            "bpw": self.bpw,
            "context_length": self.context_length,
            "tensor_size_mb": round(self.tensor_size_mb, 2),
            "kv_cache_size_mb": round(self.kv_cache_size_mb, 2),
            "overhead_mb": round(self.overhead_mb, 2),
            "total_required_mb": round(self.total_required_mb, 2),
            "available_vram_mb": round(self.available_vram_mb, 2),
            "available_ram_mb": round(self.available_ram_mb, 2),
            "fits_in_vram": self.fits_in_vram,
            "fits_in_ram": self.fits_in_ram,
            "recommended_gpu_layers": self.recommended_gpu_layers,
            "total_layers": self.total_layers,
            "offload_strategy": self.offload_strategy.value,
            "headroom_mb": round(self.headroom_mb, 2),
            "estimated_speed_relative": round(self.estimated_speed_relative, 2),
        }


class ModelQuantizerBridge:
    """
    Model Quantizer Evaluator evaluating GGUF memory profiles, KV cache footprints,
    and host RAM / GPU VRAM offload viability.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profiles: Dict[str, ModelArchitectureProfile] = dict(BUILTIN_MODEL_PROFILES)

    def register_model_profile(self, profile: ModelArchitectureProfile) -> None:
        """Register or override custom model architecture profile."""
        with self._lock:
            self._profiles[profile.name.lower()] = profile

    def get_model_profile(self, model_name: str) -> Optional[ModelArchitectureProfile]:
        """Fetch model architecture profile by name."""
        with self._lock:
            return self._profiles.get(model_name.lower())

    def list_supported_models(self) -> List[str]:
        """List all registered model architecture names."""
        with self._lock:
            return sorted(self._profiles.keys())

    def evaluate_quantization(
        self,
        model_name: str,
        quant_type: Union[QuantizationType, str],
        context_length: int = 8192,
        vram_mb: Optional[float] = None,
        ram_mb: Optional[float] = None,
        kv_cache_precision_bytes: float = 2.0,
        runtime_overhead_mb: float = 512.0,
    ) -> QuantizationEvaluationResult:
        """
        Compute detailed memory sizing and GPU offload strategy for model and quant type.
        """
        q_enum = QuantizationType(quant_type) if isinstance(quant_type, str) else quant_type
        bpw = GGUF_BPW_MAP.get(q_enum, 4.5)

        with self._lock:
            profile = self._profiles.get(model_name.lower())
            if not profile:
                raise ValueError(f"Unknown model profile '{model_name}'. Register profile first.")

        # Tensor weights size in MB
        weight_bytes = calculate_weight_size_bytes(profile.param_count, bpw)
        tensor_mb = weight_bytes / (1024 * 1024)

        # KV cache size in MB
        kv_bytes = calculate_kv_cache_bytes(
            profile.num_layers,
            profile.num_kv_heads,
            profile.head_dim,
            context_length,
            bytes_per_element=kv_cache_precision_bytes,
        )
        kv_mb = kv_bytes / (1024 * 1024)

        total_mb = tensor_mb + kv_mb + runtime_overhead_mb

        # Default hardware constraints if not provided: 8GB VRAM, 16GB RAM
        avail_vram = 8192.0 if vram_mb is None else float(vram_mb)
        avail_ram = 16384.0 if ram_mb is None else float(ram_mb)

        fits_vram = total_mb <= (avail_vram * 0.95)
        fits_ram = total_mb <= (avail_ram * 0.90)

        # Offload strategy and layer computation
        recommended_layers = 0
        strategy = OffloadStrategy.INSUFFICIENT_MEMORY
        speed_relative = 1.0

        if fits_vram:
            strategy = OffloadStrategy.FULL_GPU
            recommended_layers = profile.num_layers
            speed_relative = 1.0
            headroom = avail_vram - total_mb
        elif avail_vram > (kv_mb + runtime_overhead_mb + 1024.0):
            # Partial offload: offload as many layers as fit in available VRAM
            vram_for_weights = max(0.0, avail_vram - kv_mb - runtime_overhead_mb)
            layer_weight_mb = tensor_mb / profile.num_layers
            recommended_layers = min(profile.num_layers, int(vram_for_weights / layer_weight_mb))
            strategy = OffloadStrategy.PARTIAL_OFFLOAD
            speed_relative = max(0.2, recommended_layers / profile.num_layers * 0.8)
            headroom = (avail_vram + avail_ram) - total_mb
        elif fits_ram:
            strategy = OffloadStrategy.CPU_ONLY
            recommended_layers = 0
            speed_relative = 0.15
            headroom = avail_ram - total_mb
        else:
            strategy = OffloadStrategy.INSUFFICIENT_MEMORY
            recommended_layers = 0
            speed_relative = 0.0
            headroom = (avail_vram + avail_ram) - total_mb

        return QuantizationEvaluationResult(
            model_name=profile.name,
            quant_type=q_enum.value,
            bpw=bpw,
            context_length=context_length,
            tensor_size_mb=tensor_mb,
            kv_cache_size_mb=kv_mb,
            overhead_mb=runtime_overhead_mb,
            total_required_mb=total_mb,
            available_vram_mb=avail_vram,
            available_ram_mb=avail_ram,
            fits_in_vram=fits_vram,
            fits_in_ram=fits_ram,
            recommended_gpu_layers=recommended_layers,
            total_layers=profile.num_layers,
            offload_strategy=strategy,
            headroom_mb=headroom,
            estimated_speed_relative=speed_relative,
        )

    def compare_all_quants(
        self,
        model_name: str,
        context_length: int = 8192,
        vram_mb: Optional[float] = None,
        ram_mb: Optional[float] = None,
    ) -> List[QuantizationEvaluationResult]:
        """Compare memory and offload metrics across all standard quantization types."""
        results = []
        for q in QuantizationType:
            res = self.evaluate_quantization(
                model_name=model_name,
                quant_type=q,
                context_length=context_length,
                vram_mb=vram_mb,
                ram_mb=ram_mb,
            )
            results.append(res)
        return sorted(results, key=lambda r: r.bpw, reverse=True)

    def recommend_optimal_quant(
        self,
        model_name: str,
        context_length: int = 8192,
        vram_mb: Optional[float] = None,
        ram_mb: Optional[float] = None,
    ) -> Optional[QuantizationEvaluationResult]:
        """
        Select highest-fidelity quant (highest BPW) that achieves FULL_GPU offload,
        or optimal PARTIAL_OFFLOAD if full GPU offload is impossible.
        """
        evals = self.compare_all_quants(model_name, context_length, vram_mb, ram_mb)

        # 1. Prefer highest BPW with FULL_GPU
        full_gpu = [e for e in evals if e.offload_strategy == OffloadStrategy.FULL_GPU]
        if full_gpu:
            return full_gpu[0]

        # 2. Prefer highest layer offload with PARTIAL_OFFLOAD
        partial = [e for e in evals if e.offload_strategy == OffloadStrategy.PARTIAL_OFFLOAD]
        if partial:
            # Sort by recommended layers descending, then bpw descending
            partial.sort(key=lambda e: (e.recommended_gpu_layers, e.bpw), reverse=True)
            return partial[0]

        # 3. CPU_ONLY fallback
        cpu_only = [e for e in evals if e.offload_strategy == OffloadStrategy.CPU_ONLY]
        if cpu_only:
            return cpu_only[0]

        return None


_GLOBAL_QUANTIZER_BRIDGE: Optional[ModelQuantizerBridge] = None
_GLOBAL_QUANT_LOCK = threading.RLock()


def get_quantizer_bridge() -> ModelQuantizerBridge:
    """Acquire thread-safe singleton ModelQuantizerBridge."""
    global _GLOBAL_QUANTIZER_BRIDGE
    with _GLOBAL_QUANT_LOCK:
        if _GLOBAL_QUANTIZER_BRIDGE is None:
            _GLOBAL_QUANTIZER_BRIDGE = ModelQuantizerBridge()
        return _GLOBAL_QUANTIZER_BRIDGE


def reset_quantizer_bridge() -> ModelQuantizerBridge:
    """Reset singleton ModelQuantizerBridge."""
    global _GLOBAL_QUANTIZER_BRIDGE
    with _GLOBAL_QUANT_LOCK:
        _GLOBAL_QUANTIZER_BRIDGE = ModelQuantizerBridge()
        return _GLOBAL_QUANTIZER_BRIDGE
