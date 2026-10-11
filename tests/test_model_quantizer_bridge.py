"""
Integration test suite for Hydra Desktop Model Quantization Evaluator.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest

from desktop.model_quantizer_bridge import (
    ModelQuantizerBridge,
    ModelArchitectureProfile,
    QuantizationType,
    OffloadStrategy,
    calculate_weight_size_bytes,
    calculate_kv_cache_bytes,
    get_quantizer_bridge,
    reset_quantizer_bridge,
)


@pytest.fixture
def bridge():
    b = ModelQuantizerBridge()
    yield b


def test_calculate_weight_size_and_kv_cache_scaling():
    """Verify mathematical precision of tensor size and KV cache formulas."""
    # 8 Billion parameters at 4.0 bits per weight = 4 GB = 4,000,000,000 bytes
    weights = calculate_weight_size_bytes(8_000_000_000, 4.0)
    assert weights == 4_000_000_000

    # KV Cache formula: 2 (K+V) * layers * kv_heads * head_dim * context * 2 bytes (FP16)
    # 32 layers, 8 kv heads, 128 head dim, 8192 context
    # 2 * 32 * 8 * 128 * 8192 * 2 = 1,073,741,824 bytes = 1024 MB
    kv_bytes = calculate_kv_cache_bytes(
        num_layers=32,
        num_kv_heads=8,
        head_dim=128,
        context_length=8192,
        bytes_per_element=2.0,
    )
    assert kv_bytes == 1_073_741_824

    # Scaling context length directly scales KV cache linearly
    kv_doubled = calculate_kv_cache_bytes(32, 8, 128, 16384, 2.0)
    assert kv_doubled == kv_bytes * 2


def test_evaluate_quantization_fit_and_offload_strategy(bridge: ModelQuantizerBridge):
    """Verify evaluation returns correct tensor size, KV cache, and GPU offload strategy."""
    res = bridge.evaluate_quantization(
        model_name="llama-3.1-8b",
        quant_type=QuantizationType.Q4_K_M,
        context_length=8192,
        vram_mb=12288.0,  # 12 GB VRAM
        ram_mb=32768.0,   # 32 GB RAM
    )

    assert res.model_name == "llama-3.1-8b"
    assert res.quant_type == "Q4_K_M"
    assert res.bpw == 4.5
    assert res.tensor_size_mb > 4000.0  # ~4.3 GB
    assert res.kv_cache_size_mb == 1024.0
    assert res.fits_in_vram is True
    assert res.offload_strategy == OffloadStrategy.FULL_GPU
    assert res.recommended_gpu_layers == 32
    assert res.headroom_mb > 0

    d = res.to_dict()
    assert "tensor_size_mb" in d
    assert "offload_strategy" in d


def test_partial_offload_when_vram_constrained(bridge: ModelQuantizerBridge):
    """Verify partial offload strategy when model exceeds VRAM but fits in combined RAM."""
    res = bridge.evaluate_quantization(
        model_name="llama-3.1-70b",
        quant_type=QuantizationType.Q4_K_M,
        context_length=4096,
        vram_mb=8192.0,   # 8 GB VRAM (cannot fit 40 GB 70B model)
        ram_mb=65536.0,  # 64 GB RAM
    )

    assert res.fits_in_vram is False
    assert res.offload_strategy == OffloadStrategy.PARTIAL_OFFLOAD
    assert 0 < res.recommended_gpu_layers < res.total_layers


def test_compare_all_quants(bridge: ModelQuantizerBridge):
    """Verify comparing all quantization types sorts by BPW descending."""
    all_quants = bridge.compare_all_quants("llama-3.1-8b", context_length=8192, vram_mb=8192.0)
    assert len(all_quants) >= 10
    # First item should have highest BPW (F16)
    assert all_quants[0].quant_type == "F16"
    assert all_quants[0].bpw == 16.0
    # Last item should have lowest BPW
    assert all_quants[-1].bpw <= 2.6


def test_recommend_optimal_quant(bridge: ModelQuantizerBridge):
    """Verify recommendation selects best viable quantization format."""
    rec = bridge.recommend_optimal_quant("llama-3.1-8b", context_length=8192, vram_mb=8192.0)
    assert rec is not None
    # On 8GB VRAM with ~1GB KV and 512MB overhead, Q4_K_M or Q5_K_S should fit
    assert rec.offload_strategy == OffloadStrategy.FULL_GPU
    assert rec.recommended_gpu_layers == 32


def test_custom_profile_registration(bridge: ModelQuantizerBridge):
    """Verify registering custom model architecture profile."""
    custom = ModelArchitectureProfile(
        name="custom-moe-4b",
        param_count=4_200_000_000,
        num_layers=24,
        num_kv_heads=4,
        head_dim=128,
        default_context_length=4096,
    )
    bridge.register_model_profile(custom)
    assert "custom-moe-4b" in bridge.list_supported_models()

    res = bridge.evaluate_quantization("custom-moe-4b", QuantizationType.Q4_0, vram_mb=6144.0)
    assert res.total_layers == 24
    assert res.fits_in_vram is True


def test_global_singleton_quantizer():
    """Verify singleton lifecycle for ModelQuantizerBridge."""
    b1 = get_quantizer_bridge()
    b2 = get_quantizer_bridge()
    assert b1 is b2

    b3 = reset_quantizer_bridge()
    assert b3 is not b1
    assert get_quantizer_bridge() is b3
