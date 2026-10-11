"""
Unit tests for Hydra Desktop Runtime LoRA Adapter Hotloader Subsystem.
Verifies dynamic adapter mounting, alpha scaling, base weight non-destructive blending,
multi-adapter composition, and VRAM memory footprint tracking.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest
from desktop.model_lora_adapter_hotloader import (
    AdapterStatus,
    LoraAdapterRecord,
    LoraLayerWeights,
    ModelLoraAdapterHotloader,
    compute_lora_delta_matrix,
    get_lora_hotloader,
    reset_lora_hotloader,
)


def test_lora_layer_weights_and_delta():
    # in_features = 2, out_features = 2, rank = 1
    # A = [[1.0, 2.0]], B = [[3.0], [4.0]]
    layer = LoraLayerWeights(
        layer_name="q_proj",
        in_features=2,
        out_features=2,
        rank=1,
        matrix_a=[[1.0, 2.0]],
        matrix_b=[[3.0], [4.0]],
        bytes_per_param=4,
    )
    # Parameter count: 2 * 1 + 2 * 1 = 4 parameters -> 16 bytes
    assert layer.parameter_count == 4
    assert layer.memory_bytes == 16

    # B x A = [[3*1, 3*2], [4*1, 4*2]] = [[3.0, 6.0], [4.0, 8.0]]
    # With scaling factor 0.5: [[1.5, 3.0], [2.0, 4.0]]
    delta = compute_lora_delta_matrix(layer, scaling_factor=0.5)
    assert delta == [[1.5, 3.0], [2.0, 4.0]]


def test_mounting_and_scaling_factors():
    hotloader = ModelLoraAdapterHotloader()
    layer = LoraLayerWeights(
        layer_name="v_proj",
        in_features=2,
        out_features=2,
        rank=2,
        matrix_a=[[1.0, 0.0], [0.0, 1.0]],
        matrix_b=[[1.0, 0.0], [0.0, 1.0]],
    )

    rec = hotloader.mount_adapter(
        adapter_id="lora_code_opt",
        name="Code Optimizer",
        rank=2,
        alpha=4.0,
        layers={"v_proj": layer},
        scale_multiplier=1.0,
    )
    assert rec.status == AdapterStatus.MOUNTED
    # gamma = 4.0 / 2 = 2.0
    assert rec.scaling_factor == 2.0

    hotloader.set_scale_multiplier("lora_code_opt", 0.5)
    assert rec.scaling_factor == 1.0

    data = rec.to_dict()
    assert data["adapter_id"] == "lora_code_opt"
    assert data["layer_count"] == 1
    assert data["memory_bytes"] > 0


def test_non_destructive_layer_forward():
    hotloader = ModelLoraAdapterHotloader()

    # Identity base matrix
    base_w = [
        [1.0, 0.0],
        [0.0, 1.0],
    ]
    base_w_copy = [list(row) for row in base_w]

    layer = LoraLayerWeights(
        layer_name="linear1",
        in_features=2,
        out_features=2,
        rank=1,
        matrix_a=[[1.0, 0.0]],
        matrix_b=[[0.5], [0.0]],
    )

    hotloader.mount_adapter(
        adapter_id="test_lora",
        name="Test",
        rank=1,
        alpha=1.0,  # gamma = 1.0
        layers={"linear1": layer},
    )

    x = [2.0, 3.0]
    # base output: [2.0, 3.0]
    # lora output: A * x = 2.0; B * 2.0 = [1.0, 0.0]
    # total output: [2.0 + 1.0, 3.0 + 0.0] = [3.0, 3.0]
    y = hotloader.apply_layer_forward("linear1", base_w, x)
    assert y == [3.0, 3.0]

    # Verify base matrix remains completely unaltered
    assert base_w == base_w_copy


def test_blending_weights_matrix():
    hotloader = ModelLoraAdapterHotloader()

    base_w = [
        [1.0, 2.0],
        [3.0, 4.0],
    ]
    layer = LoraLayerWeights(
        layer_name="dense",
        in_features=2,
        out_features=2,
        rank=1,
        matrix_a=[[1.0, 1.0]],
        matrix_b=[[2.0], [2.0]],
    )
    # B x A = [[2.0, 2.0], [2.0, 2.0]]
    # gamma = alpha / r = 2.0 / 1 = 2.0
    # delta = [[4.0, 4.0], [4.0, 4.0]]
    hotloader.mount_adapter(
        adapter_id="adapter_1",
        name="Delta",
        rank=1,
        alpha=2.0,
        layers={"dense": layer},
    )

    blended = hotloader.blend_layer_weights("dense", base_w)
    assert blended == [
        [5.0, 6.0],
        [7.0, 8.0],
    ]
    # Pristine base intact
    assert base_w[0][0] == 1.0


def test_multi_adapter_composition_and_unmount():
    hotloader = ModelLoraAdapterHotloader()

    base_w = [[1.0, 0.0], [0.0, 1.0]]
    layer_a = LoraLayerWeights("l1", 2, 2, 1, [[1.0, 0.0]], [[1.0], [0.0]])
    layer_b = LoraLayerWeights("l1", 2, 2, 1, [[0.0, 1.0]], [[0.0], [1.0]])

    hotloader.mount_adapter("adapt_a", "A", 1, 1.0, {"l1": layer_a})
    hotloader.mount_adapter("adapt_b", "B", 1, 1.0, {"l1": layer_b})

    x = [2.0, 3.0]
    # base: [2, 3]
    # A adds: [2, 0]
    # B adds: [0, 3]
    # total: [4, 6]
    y_both = hotloader.apply_layer_forward("l1", base_w, x)
    assert y_both == [4.0, 6.0]

    # Run with only adapter A
    y_only_a = hotloader.apply_layer_forward("l1", base_w, x, active_adapter_ids=["adapt_a"])
    assert y_only_a == [4.0, 3.0]

    # Unmount adapter B
    unmounted = hotloader.unmount_adapter("adapt_b")
    assert unmounted is not None
    assert unmounted.status == AdapterStatus.UNMOUNTED

    y_after = hotloader.apply_layer_forward("l1", base_w, x)
    assert y_after == [4.0, 3.0]


def test_vram_budget_and_singleton():
    hotloader = ModelLoraAdapterHotloader(max_vram_budget_bytes=1000)
    layer = LoraLayerWeights("l1", 10, 10, 2, [[0.0]*10]*2, [[0.0]*2]*10, bytes_per_param=4)
    # params = 20 + 20 = 40; bytes = 160
    rec = hotloader.mount_adapter("small_lora", "Small", 2, 1.0, {"l1": layer})
    assert hotloader.get_allocated_memory_bytes() == 160

    h1 = get_lora_hotloader()
    h2 = get_lora_hotloader()
    assert h1 is h2

    h3 = reset_lora_hotloader()
    assert h3 is not h1
    assert get_lora_hotloader() is h3
